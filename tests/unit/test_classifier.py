"""Unit tests for automation/job_classifier.py."""
import pytest
from automation.job_classifier import classify_job

def test_classify_backend():
    assert classify_job("Backend Developer") == "Backend"
    assert classify_job("Java Engineer") == "Backend"
    assert classify_job("Python Developer") == "Backend"
    assert classify_job("Django Specialist") == "Backend"
    assert classify_job("Senior .NET Developer") == "Backend"

def test_classify_frontend():
    assert classify_job("Frontend Engineer") == "Frontend"
    assert classify_job("React Developer") == "Frontend"
    assert classify_job("Angular Dev") == "Frontend"
    assert classify_job("Javascript specialist") == "Frontend"

def test_classify_fullstack():
    assert classify_job("Fullstack Developer") == "Full-stack"
    assert classify_job("Full-stack Engineer") == "Full-stack"
    assert classify_job("Software Engineer Full Stack") == "Full-stack"

def test_classify_dataml():
    assert classify_job("Data Scientist") == "Data/ML"
    assert classify_job("Machine Learning Engineer") == "Data/ML"
    assert classify_job("BI Analyst") == "Data/ML"
    assert classify_job("Data Engineer") == "Data/ML"

def test_classify_devops():
    assert classify_job("DevOps Engineer") == "DevOps/Cloud"
    assert classify_job("SRE") == "DevOps/Cloud"
    assert classify_job("Cloud Architect") == "DevOps/Cloud"

def test_classify_qa():
    assert classify_job("QA Engineer") == "QA"
    assert classify_job("Software Tester") == "QA"
    assert classify_job("Quality Assurance Specialist") == "QA"

def test_classify_mobile():
    assert classify_job("iOS Developer") == "Mobile"
    assert classify_job("Android Engineer") == "Mobile"
    assert classify_job("Flutter Developer") == "Mobile"

def test_classify_security():
    assert classify_job("Cybersecurity Analyst") == "Security"
    assert classify_job("Information Security Officer") == "Security"

def test_classify_itinfra():
    assert classify_job("IT Support Technician") == "IT/Infra"
    assert classify_job("System Administrator") == "IT/Infra"
    assert classify_job("Sysadmin") == "IT/Infra"

def test_classify_othertech():
    assert classify_job("Software Architect") == "Other-tech"
    assert classify_job("CTO") == "Other-tech"
    assert classify_job("IT Director") == "Other-tech"

def test_classify_nontech():
    assert classify_job("Marketing Manager") == "Non-tech"
    assert classify_job("Sales Associate") == "Non-tech"
    assert classify_job("HR Specialist") == "Non-tech"
    # Even if they have 'tech' words, if it is primarily marketing
    assert classify_job("Digital Marketing Manager") == "Non-tech"

def test_classify_fallback_to_description():
    # If title is vague but description has react/frontend words
    assert classify_job("Consultor de Vagas", "We are looking for a developer with React, Javascript, and Vue experience.") == "Frontend"
    assert classify_job("Estágio Profissional", "Looking for someone experienced in Python, Java, and Django.") == "Backend"

def test_classify_edge_cases():
    assert classify_job("Backend Developer (Node.js)") == "Backend"
    assert classify_job("Software Developer (.NET)") == "Backend"
    assert classify_job("NodeJS Developer") == "Backend"
    assert classify_job("Go Developer") == "Backend"
    assert classify_job("Golang/Go Developer") == "Backend"
    assert classify_job("R Specialist") == "Backend"
    assert classify_job("C++ Engineer") == "Backend"
    assert classify_job("C# Specialist") == "Backend"
    assert classify_job("Fullstack Developer c/ experiência") == "Full-stack"
    # Test description check exclusions for ambiguous words
    assert classify_job("Consultor de Vagas", "Looking for a candidate with R & D background who can go to market.") == "Non-tech"
    assert classify_job("Consultor de Vagas", "Looking for a developer with R & D background who can go to market.") == "Other-tech"


def test_bigdata_substring_collision():
    """Fix: 'big data' alias must come before 'data engineer' to avoid partial replacement."""
    # These previously classified as Backend (java match) because the tokenizer
    # turned 'big data engineer' into 'big dataengineer' instead of 'bigdataengineer'.
    assert classify_job("Big Data Engineer - Spark / Scala / Java") == "Data/ML"
    assert classify_job("Big Data Engineer") == "Data/ML"
    assert classify_job("Senior Big Data Engineer") == "Data/ML"
    # Plain 'big data' without 'engineer' was turning Non-tech (no role anchor)
    assert classify_job("Big Data Analyst") == "Data/ML"
    assert classify_job("Senior Data Engineer (Big Data)") == "Data/ML"


def test_backend_role_over_infra_tech():
    """Fix: explicit role labels (Backend/Frontend/etc.) must win over infra-tech tokens (AWS/Azure/GCP).
    A 'Backend Engineer who uses AWS' is Backend, not DevOps/Cloud."""
    assert classify_job("Backend Engineer (Node.js, AWS)") == "Backend"
    assert classify_job("Node.js Backend Engineer AWS") == "Backend"
    assert classify_job("Senior Backend Engineer - AWS, Node.js") == "Backend"
    assert classify_job("Frontend Developer (AWS)") == "Frontend"
    # Pure infra-tech with no role label → still DevOps/Cloud
    assert classify_job("AWS Cloud Specialist") == "DevOps/Cloud"
    assert classify_job("Azure Infrastructure Engineer") == "IT/Infra"  # 'infrastructure' is an IT/Infra role token
    # Explicit DevOps role tokens always win
    assert classify_job("DevOps Engineer AWS") == "DevOps/Cloud"
    assert classify_job("SRE - AWS / GCP") == "DevOps/Cloud"

