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
